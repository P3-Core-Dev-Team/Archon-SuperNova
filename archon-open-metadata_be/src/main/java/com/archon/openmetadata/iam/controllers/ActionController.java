package com.archon.openmetadata.iam.controllers;

import com.archon.openmetadata.iam.dto.ActionDto;
import com.archon.openmetadata.iam.dto.ActionFilterBean;
import com.archon.openmetadata.iam.models.Action;
import com.archon.openmetadata.iam.services.ActionService;
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
@RequestMapping("/api/v1/actions")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class ActionController {

  private final ActionService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<Action> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<ActionDto>>> fetchAll(Pageable pageable) {
    Page<Action> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, ActionDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ActionController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<ActionDto>> getById(@PathVariable UUID id) {
    Action entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, ActionDto.class),
            WebMvcLinkBuilder.linkTo(WebMvcLinkBuilder.methodOn(ActionController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<ActionDto>> create(@RequestBody ActionDto dto) {
    Action saved = service.save(modelMapper.map(dto, Action.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, ActionDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ActionController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<ActionDto>> update(
      @PathVariable UUID id, @RequestBody ActionDto dto) {
    dto.setId(id);
    Action updated = service.save(modelMapper.map(dto, Action.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, ActionDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ActionController.class).getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<ActionDto>>> searchAll(
      @RequestBody ActionFilterBean filterBean, Pageable pageable) {
    Page<Action> page =
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
                    modelMapper.map(entity, ActionDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ActionController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
