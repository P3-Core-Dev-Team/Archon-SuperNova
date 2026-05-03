package com.archon.openmetadata.metadata.controllers;

import com.archon.openmetadata.metadata.dto.TableEntityDto;
import com.archon.openmetadata.metadata.dto.TableEntityFilterBean;
import com.archon.openmetadata.metadata.models.TableEntity;
import com.archon.openmetadata.metadata.services.TableEntityService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/tables")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class TableEntityController {

  private final TableEntityService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<TableEntity> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<TableEntityDto>>> fetchAll(Pageable pageable) {
    Page<TableEntity> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, TableEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(TableEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<TableEntityDto>> getById(@PathVariable UUID id) {
    TableEntity entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, TableEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(TableEntityController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<TableEntityDto>> create(@RequestBody TableEntityDto dto) {
    TableEntity saved = service.save(modelMapper.map(dto, TableEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, TableEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(TableEntityController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<TableEntityDto>> update(
      @PathVariable UUID id, @RequestBody TableEntityDto dto) {
    dto.setId(id);
    TableEntity updated = service.save(modelMapper.map(dto, TableEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, TableEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(TableEntityController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<TableEntityDto>>> searchAll(
      @RequestBody TableEntityFilterBean filterBean, Pageable pageable) {
    Page<TableEntity> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, TableEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(TableEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
